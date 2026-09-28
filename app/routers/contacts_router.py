"""
CRM Contacts Router — BookaBoost

Real contact management endpoints (distinct from the webhook connector
crm_router.py which handles GoHighLevel / HubSpot push integrations).

Endpoints:
  GET    /crm/contacts                  list org contacts
  POST   /crm/contacts                  create a contact
  PATCH  /crm/contacts/{id}             update a contact
  DELETE /crm/contacts/{id}             delete a contact
  GET    /crm/contacts/{id}/notes       list notes for a contact
  POST   /crm/contacts/{id}/notes       add a note to a contact
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.deps import get_db, get_current_user, require_tenant_user
from app.models.models import User
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/crm", tags=["crm-contacts"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ContactCreate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    pipeline_stage: str = "new"

class ContactUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    pipeline_stage: Optional[str] = None
    last_contact_at: Optional[datetime] = None

class NoteCreate(BaseModel):
    content: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _org_id(db: Session, current_user: User):
    """The ACTIVE workspace org (X-Workspace-Id backed by a membership, else
    the home column). Every route here used `current_user.organization_id`, so
    a person standing in workspace B listed, edited, deleted and created HOME
    org A's contacts, and a membership-only member (no home column) inserted
    rows owned by NULL. Without X-Workspace-Id this is the home column,
    exactly as before. A contact outside the workspace answers 404."""
    from app.services.lead_scope import active_workspace_org_id
    return active_workspace_org_id(current_user, db)


def _row_to_dict(row) -> dict:
    """Convert a RowMapping to a plain dict."""
    return dict(row._mapping) if hasattr(row, '_mapping') else dict(row)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/contacts")
def list_contacts(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    rows = db.execute(text(
        "SELECT * FROM crm_contacts WHERE organization_id = :org_id ORDER BY created_at DESC"
        " LIMIT :limit OFFSET :offset"
    ), {"org_id": _org_id(db, current_user), "limit": limit, "offset": offset}).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post("/contacts", status_code=201)
def create_contact(
    payload: ContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    org_id = _org_id(db, current_user)
    if not org_id:
        from app.services.platform_owner import _NO_CONTEXT_DETAIL
        raise HTTPException(status_code=409, detail=_NO_CONTEXT_DETAIL)
    contact_id = str(uuid.uuid4())
    db.execute(text(
        """INSERT INTO crm_contacts
           (id, organization_id, created_by_id, full_name, phone, email, company, pipeline_stage, created_at)
           VALUES (:id, :org_id, :user_id, :full_name, :phone, :email, :company, :stage, NOW())"""
    ), {
        "id": contact_id,
        "org_id": org_id,
        "user_id": current_user.id,
        "full_name": payload.full_name,
        "phone": payload.phone,
        "email": payload.email,
        "company": payload.company,
        "stage": payload.pipeline_stage,
    })
    db.commit()
    row = db.execute(text("SELECT * FROM crm_contacts WHERE id = :id"), {"id": contact_id}).fetchone()
    return _row_to_dict(row)


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    row = db.execute(text(
        "SELECT * FROM crm_contacts WHERE id = :id AND organization_id = :org_id"
    ), {"id": contact_id, "org_id": _org_id(db, current_user)}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return _row_to_dict(row)

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["contact_id"] = contact_id
    updates["org_id"] = row._mapping["organization_id"]
    db.execute(text(f"UPDATE crm_contacts SET {set_clauses} "
                    f"WHERE id = :contact_id AND organization_id = :org_id"), updates)
    db.commit()

    updated = db.execute(text("SELECT * FROM crm_contacts WHERE id = :id"), {"id": contact_id}).fetchone()
    return _row_to_dict(updated)

@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    # A HARD DELETE (contact and its notes), so manager authority IN THIS
    # WORKSPACE is required - any tenant user could delete any contact here.
    # Platform operators keep their platform-role pass.
    from app.services.lead_scope import is_manager_here
    if not (current_user.role in ("super_admin", "god_admin")
            or is_manager_here(current_user, db)):
        raise HTTPException(status_code=403, detail="Admin access required")
    row = db.execute(text(
        # SELECT * rather than named columns: this table's shape differs between
        # deployments (full_name vs first_name/last_name), and the snapshot
        # below must never be the reason a delete fails.
        "SELECT * FROM crm_contacts WHERE id = :id AND organization_id = :org_id"
    ), {"id": contact_id, "org_id": _org_id(db, current_user)}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")
    snapshot = _row_to_dict(row)
    db.execute(text("DELETE FROM crm_contact_notes WHERE contact_id = :id"), {"id": contact_id})
    db.execute(text("DELETE FROM crm_contacts WHERE id = :id"), {"id": contact_id})
    # Recorded in the same transaction as the delete; the org is the row's own.
    log_action(db, snapshot["organization_id"], current_user.id,
               action="crm_contact.deleted", target_type="crm_contact",
               target_id=contact_id,
               before={"full_name": snapshot.get("full_name"),
                       "first_name": snapshot.get("first_name"),
                       "last_name": snapshot.get("last_name"),
                       "email": snapshot.get("email"),
                       "phone": snapshot.get("phone")},
               commit=False)
    db.commit()
    return None


@router.get("/contacts/{contact_id}/notes")
def list_notes(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    # Verify org ownership
    row = db.execute(text(
        "SELECT id FROM crm_contacts WHERE id = :id AND organization_id = :org_id"
    ), {"id": contact_id, "org_id": _org_id(db, current_user)}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")

    notes = db.execute(text(
        "SELECT * FROM crm_contact_notes WHERE contact_id = :cid ORDER BY created_at DESC"
    ), {"cid": contact_id}).fetchall()
    return [_row_to_dict(n) for n in notes]


@router.post("/contacts/{contact_id}/notes", status_code=201)
def add_note(
    contact_id: str,
    payload: NoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    row = db.execute(text(
        "SELECT id FROM crm_contacts WHERE id = :id AND organization_id = :org_id"
    ), {"id": contact_id, "org_id": _org_id(db, current_user)}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")

    note_id = str(uuid.uuid4())
    db.execute(text(
        """INSERT INTO crm_contact_notes (id, contact_id, created_by_id, content, created_at)
           VALUES (:id, :contact_id, :user_id, :content, NOW())"""
    ), {"id": note_id, "contact_id": contact_id, "user_id": current_user.id, "content": payload.content.strip()})
    # Update last_contact_at on the parent contact
    db.execute(text(
        "UPDATE crm_contacts SET last_contact_at = NOW() WHERE id = :id"
    ), {"id": contact_id})
    db.commit()

    note = db.execute(text("SELECT * FROM crm_contact_notes WHERE id = :id"), {"id": note_id}).fetchone()
    return _row_to_dict(note)
