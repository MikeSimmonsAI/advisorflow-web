"""
Native CRM Router — master contact records.

CRM contacts are richer, long-lived relationship records that live alongside
leads but are independent of them. A contact can optionally link to a Lead.

This is DISTINCT from crm_router.py which handles external CRM webhook
integrations (GoHighLevel, HubSpot, Zapier). This router handles the
internal native CRM contacts stored in crm_contacts.

Stages default to funeral-industry appropriate values but are designed to be
org-configurable (future work: org_crm_stages setting on Organization model).

Default stages:
  inquiry → pre_need → at_need → arrangements → services_complete → aftercare → closed
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, CRMContact, CRMNote, Lead

router = APIRouter(prefix="/crm-native", tags=["crm-native"])

# Default stages for funeral industry.
# Future: pull from org.crm_stages if set.
DEFAULT_STAGES = [
    {"key": "inquiry",           "label": "Inquiry",              "color": "#64748b"},
    {"key": "pre_need",          "label": "Pre-Need",             "color": "#6366f1"},
    {"key": "at_need",           "label": "At-Need",              "color": "#f59e0b"},
    {"key": "arrangements",      "label": "Arrangements",         "color": "#ef4444"},
    {"key": "services_complete", "label": "Services Complete",    "color": "#10b981"},
    {"key": "aftercare",         "label": "Aftercare Follow-up",  "color": "#3b82f6"},
    {"key": "closed",            "label": "Closed",               "color": "#374151"},
]


def _is_manager(user: User) -> bool:
    return user.role in ("org_admin", "super_admin", "god_admin")


def _contact_dict(c: CRMContact) -> dict:
    return {
        "id": c.id,
        "organization_id": c.organization_id,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "full_name": f"{c.first_name or ''} {c.last_name or ''}".strip() or "—",
        "phone": c.phone,
        "email": c.email,
        "address_street": c.address_street,
        "address_city": c.address_city,
        "address_state": c.address_state,
        "address_zip": c.address_zip,
        "stage": c.stage,
        "notes": c.notes,
        "tags": c.tags,
        "lead_id": c.lead_id,
        "assigned_to_id": c.assigned_to_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "last_contacted_at": c.last_contacted_at.isoformat() if c.last_contacted_at else None,
        "is_archived": c.is_archived,
    }


def _base_query(db: Session, user: User):
    q = db.query(CRMContact).filter(
        CRMContact.organization_id == user.organization_id,
        CRMContact.is_archived == False,
    )
    if not _is_manager(user):
        q = q.filter(CRMContact.assigned_to_id == user.id)
    return q


@router.get("/stages")
def get_stages(current_user: User = Depends(get_current_user)):
    return DEFAULT_STAGES


@router.get("/contacts")
def list_contacts(
    stage: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = _base_query(db, current_user)
    if stage:
        q = q.filter(CRMContact.stage == stage)
    if search:
        like = f"%{search.lower()}%"
        from sqlalchemy import or_, func
        q = q.filter(or_(
            func.lower(CRMContact.first_name).like(like),
            func.lower(CRMContact.last_name).like(like),
            CRMContact.phone.like(like),
            func.lower(CRMContact.email).like(like),
        ))
    total = q.count()
    contacts = q.order_by(CRMContact.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_contact_dict(c) for c in contacts]}


@router.get("/contacts/{contact_id}")
def get_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    result = _contact_dict(contact)
    if contact.lead_id:
        lead = db.query(Lead).filter(Lead.id == contact.lead_id, Lead.organization_id == current_user.organization_id).first()
        if lead:
            result["linked_lead"] = {"id": lead.id, "status": lead.status, "tier": lead.tier}
    return result


class ContactCreate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    stage: Optional[str] = "inquiry"
    notes: Optional[str] = None
    tags: Optional[str] = None
    lead_id: Optional[str] = None
    assigned_to_id: Optional[str] = None


@router.post("/contacts")
def create_contact(
    req: ContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = CRMContact(
        organization_id=current_user.organization_id,
        first_name=req.first_name,
        last_name=req.last_name,
        phone=req.phone,
        email=req.email,
        address_street=req.address_street,
        address_city=req.address_city,
        address_state=req.address_state,
        address_zip=req.address_zip,
        stage=req.stage or "inquiry",
        notes=req.notes,
        tags=req.tags,
        lead_id=req.lead_id,
        assigned_to_id=req.assigned_to_id or current_user.id,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return _contact_dict(contact)


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    stage: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    lead_id: Optional[str] = None
    assigned_to_id: Optional[str] = None


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: str,
    req: ContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    for field in ["first_name", "last_name", "phone", "email",
                  "address_street", "address_city", "address_state", "address_zip",
                  "stage", "notes", "tags", "lead_id", "assigned_to_id"]:
        val = getattr(req, field)
        if val is not None:
            setattr(contact, field, val)
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(contact)
    return _contact_dict(contact)


@router.delete("/contacts/{contact_id}")
def archive_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.is_archived = True
    contact.updated_at = datetime.utcnow()
    db.commit()
    return {"archived": True}


@router.get("/contacts/{contact_id}/notes")
def get_notes(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    notes = db.query(CRMNote).filter(CRMNote.contact_id == contact_id).order_by(CRMNote.created_at.desc()).all()
    return [{"id": n.id, "content": n.content, "created_at": n.created_at.isoformat() if n.created_at else None, "author_id": n.author_id} for n in notes]


class NoteCreate(BaseModel):
    content: str


@router.post("/contacts/{contact_id}/notes")
def add_note(
    contact_id: str,
    req: NoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    note = CRMNote(contact_id=contact_id, author_id=current_user.id, content=req.content.strip())
    db.add(note)
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return {"id": note.id, "content": note.content, "created_at": note.created_at.isoformat() if note.created_at else None, "author_id": note.author_id}


@router.post("/contacts/from-lead/{lead_id}")
def create_from_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    existing = db.query(CRMContact).filter(
        CRMContact.lead_id == lead_id,
        CRMContact.organization_id == current_user.organization_id,
        CRMContact.is_archived == False,
    ).first()
    if existing:
        return {**_contact_dict(existing), "already_existed": True}
    contact = CRMContact(
        organization_id=current_user.organization_id,
        first_name=lead.first_name,
        last_name=lead.last_name,
        phone=lead.phone,
        email=lead.email,
        stage="inquiry",
        lead_id=lead.id,
        assigned_to_id=lead.assigned_to_id or current_user.id,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return {**_contact_dict(contact), "already_existed": False}


@router.post("/sync-from-leads")
def sync_leads_to_crm(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk-sync all org leads into the CRM — creates a CRMContact for every
    lead that doesn't already have one.  Safe to call multiple times; leads
    already in CRM are silently skipped.  Only syncs non-duplicate leads.
    """
    # Collect all lead IDs that already have a CRM contact
    existing_lead_ids = {
        row.lead_id
        for row in db.query(CRMContact.lead_id).filter(
            CRMContact.organization_id == current_user.organization_id,
            CRMContact.lead_id.isnot(None),
            CRMContact.is_archived == False,
        ).all()
        if row.lead_id
    }

    leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.is_duplicate == False,
        )
        .all()
    )

    created = 0
    for lead in leads:
        if lead.id in existing_lead_ids:
            continue
        contact = CRMContact(
            organization_id=current_user.organization_id,
            first_name=lead.first_name,
            last_name=lead.last_name,
            phone=lead.phone,
            email=lead.email,
            stage="inquiry",
            lead_id=lead.id,
            assigned_to_id=lead.assigned_to_id or current_user.id,
        )
        db.add(contact)
        created += 1

    if created:
        db.commit()

    return {
        "synced": created,
        "already_existed": len(leads) - created,
        "total_leads": len(leads),
    }
